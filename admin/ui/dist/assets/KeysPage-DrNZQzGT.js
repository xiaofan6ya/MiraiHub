import{h as ct,k as v,i as la,m as wa,F as Ne,c as S,r as h,w as De,o as ua,D as Sa,j as Ta,H as Pa,G as Ra,I as za,n as It,p as Ia,t as le,b as Aa,V as Z,R as c,Q as m,S as s,$ as $a,O as L,f as N,W as y,X as D,Z as nt,P as re,Y as _e}from"./vendor-vue-01Qz8dz3.js";import{G as La,I as dt,J as Ea,l as Ka,K as Oa,r as Ba,L as Na,v as Ma,M as Wa,m as p,n as w,p as O,o as U,q as ja,O as At,Q as Ht,w as Fa,x as da,R as qa,T as Ua,A as ue,z as rt,P as Ha,H as Dt,N as Ce,V as Da,C as Va,F as Ga}from"./index-BlmbHgxO.js";import{u as Xa,B as J,E as $t,a as Qa}from"./FormField.vue_vue_type_style_index_0_scoped_ab743373_lang-YEOgjsNE.js";import{I as Ja}from"./IconBtn-FQ78AQMn.js";import{C as Vt}from"./Card-DA9GNKMF.js";import{S as ot}from"./Section-BsL4QYVc.js";import{B as it}from"./Badge-Cjfjyakj.js";/* empty css                                                                  */import{C as Gt}from"./Chip-DaM1EiLP.js";import{C as st}from"./Copyable-C5AokH7h.js";import{S as Ya}from"./Segmented-q77xVJGJ.js";import{K as Za}from"./KV-D8wA8sWM.js";import{C as we}from"./CodeBlock-QhQi3jem.js";import{D as Xt,N as Lt}from"./DataTable-B-KXEXBp.js";/* empty css                                                              */import{F as en}from"./FilterBar-_MhK25c1.js";import{M as Qt}from"./Meter-BPvD9UAq.js";import{R as tn}from"./RowActions-DPMma8-J.js";import{h as ee,j as an,A as nn,_ as rn}from"./_plugin-vue_export-helper-ND5k2sci.js";import{F as on}from"./FormDrawer-D_7LJxQ2.js";import{F as de}from"./FormField-Dg1i4B4V.js";import{D as lt}from"./DetailDrawer-zV3CyqSD.js";import{e as Et,f as Oe}from"./error-Bz9KZGxV.js";import{u as sn}from"./useConfirm-0ez-s8QF.js";import{u as ln}from"./useTableQuery-DG7Bhkkg.js";import{F as Me,f as ca,e as Se,m as un,c as ge}from"./format-ByabA-4O.js";import{R as dn}from"./refresh-cw-B-LVPiOg.js";import{P as Jt}from"./plus-DpHZL1sP.js";import{A as cn}from"./Add-BYamtQox.js";import{V as Kt,E as fn,q as pn,G as vn,v as Yt,j as bn,g as ut}from"./vendor-naive-SrciwSnN.js";import{N as Be}from"./Select-DpSIQ_Kz.js";import{N as Zt}from"./Skeleton-CyrVRsRL.js";/* empty css                                                                */import"./Spin-DRBwyNHZ.js";import"./feedback-eoVbvP8x.js";/* empty css                                                                 */import"./media-DhhT9Mh5.js";import"./Forward-CyWRo3OE.js";import"./Dropdown-DaN48hoM.js";import"./x-B7N78DEg.js";import"./ellipsis-CJrGHoga.js";var mn=/\s/;function gn(e){for(var n=e.length;n--&&mn.test(e.charAt(n)););return n}var yn=/^\s+/;function hn(e){return e&&e.slice(0,gn(e)+1).replace(yn,"")}var ea=NaN,xn=/^[-+]0x[0-9a-f]+$/i,kn=/^0b[01]+$/i,_n=/^0o[0-7]+$/i,Cn=parseInt;function ta(e){if(typeof e=="number")return e;if(La(e))return ea;if(dt(e)){var n=typeof e.valueOf=="function"?e.valueOf():e;e=dt(n)?n+"":n}if(typeof e!="string")return e===0?e:+e;e=hn(e);var r=kn.test(e);return r||_n.test(e)?Cn(e.slice(2),r?2:8):xn.test(e)?ea:+e}var Ot=function(){return Ea.Date.now()},wn="Expected a function",Sn=Math.max,Tn=Math.min;function Pn(e,n,r){var d,o,f,g,k,x,P=0,z=!1,A=!1,F=!0;if(typeof e!="function")throw new TypeError(wn);n=ta(n)||0,dt(r)&&(z=!!r.leading,A="maxWait"in r,f=A?Sn(ta(r.maxWait)||0,n):f,F="trailing"in r?!!r.trailing:F);function M(_){var j=d,G=o;return d=o=void 0,P=_,g=e.apply(G,j),g}function B(_){return P=_,k=setTimeout(W,n),z?M(_):g}function K(_){var j=_-x,G=_-P,ce=n-j;return A?Tn(ce,f-G):ce}function q(_){var j=_-x,G=_-P;return x===void 0||j>=n||j<0||A&&G>=f}function W(){var _=Ot();if(q(_))return E(_);k=setTimeout(W,K(_))}function E(_){return k=void 0,F&&d?M(_):(d=o=void 0,g)}function te(){k!==void 0&&clearTimeout(k),P=0,d=x=o=k=void 0}function V(){return k===void 0?g:E(Ot())}function T(){var _=Ot(),j=q(_);if(d=arguments,o=this,x=_,j){if(k===void 0)return B(x);if(A)return clearTimeout(k),k=setTimeout(W,n),M(x)}return k===void 0&&(k=setTimeout(W,n)),g}return T.cancel=te,T.flush=V,T}var Rn="Expected a function";function zn(e,n,r){var d=!0,o=!0;if(typeof e!="function")throw new TypeError(Rn);return dt(r)&&(d="leading"in r?!!r.leading:d,o="trailing"in r?!!r.trailing:o),Pn(e,n,{leading:d,maxWait:n,trailing:o})}const Ft=Ka("n-tabs"),fa={tab:[String,Number,Object,Function],name:{type:[String,Number],required:!0},disabled:Boolean,displayDirective:{type:String,default:"if"},closable:{type:Boolean,default:void 0},tabProps:Object,label:[String,Number,Object,Function]},He=ct({__TAB_PANE__:!0,name:"TabPane",alias:["TabPanel"],props:fa,slots:Object,setup(e){const n=la(Ft,null);return n||Oa("tab-pane","`n-tab-pane` must be placed inside `n-tabs`."),{style:n.paneStyleRef,class:n.paneClassRef,mergedClsPrefix:n.mergedClsPrefixRef}},render(){return v("div",{class:[`${this.mergedClsPrefix}-tab-pane`,this.class],style:this.style},this.$slots)}}),In=Object.assign({internalLeftPadded:Boolean,internalAddable:Boolean,internalCreatedByPane:Boolean},Wa(fa,["displayDirective"])),Wt=ct({__TAB__:!0,inheritAttrs:!1,name:"Tab",props:In,setup(e){const{mergedClsPrefixRef:n,valueRef:r,typeRef:d,closableRef:o,tabStyleRef:f,addTabStyleRef:g,tabClassRef:k,addTabClassRef:x,tabChangeIdRef:P,onBeforeLeaveRef:z,triggerRef:A,handleAdd:F,activateTab:M,handleClose:B}=la(Ft);return{trigger:A,mergedClosable:S(()=>{if(e.internalAddable)return!1;const{closable:K}=e;return K===void 0?o.value:K}),style:f,addStyle:g,tabClass:k,addTabClass:x,clsPrefix:n,value:r,type:d,handleClose(K){K.stopPropagation(),!e.disabled&&B(e.name)},activateTab(){if(e.disabled)return;if(e.internalAddable){F();return}const{name:K}=e,q=++P.id;if(K!==r.value){const{value:W}=z;W?Promise.resolve(W(e.name,r.value)).then(E=>{E&&P.id===q&&M(K)}):M(K)}}}},render(){const{internalAddable:e,clsPrefix:n,name:r,disabled:d,label:o,tab:f,value:g,mergedClosable:k,trigger:x,$slots:{default:P}}=this,z=o??f;return v("div",{class:`${n}-tabs-tab-wrapper`},this.internalLeftPadded?v("div",{class:`${n}-tabs-tab-pad`}):null,v("div",Object.assign({key:r,"data-name":r,"data-disabled":d?!0:void 0},wa({class:[`${n}-tabs-tab`,g===r&&`${n}-tabs-tab--active`,d&&`${n}-tabs-tab--disabled`,k&&`${n}-tabs-tab--closable`,e&&`${n}-tabs-tab--addable`,e?this.addTabClass:this.tabClass],onClick:x==="click"?this.activateTab:void 0,onMouseenter:x==="hover"?this.activateTab:void 0,style:e?this.addStyle:this.style},this.internalCreatedByPane?this.tabProps||{}:this.$attrs)),v("span",{class:`${n}-tabs-tab__label`},e?v(Ne,null,v("div",{class:`${n}-tabs-tab__height-placeholder`}," "),v(Ba,{clsPrefix:n},{default:()=>v(cn,null)})):P?P():typeof z=="object"?z:Na(z??r)),k&&this.type==="card"?v(Ma,{clsPrefix:n,class:`${n}-tabs-tab__close`,onClick:this.handleClose,disabled:d}):null))}}),An=p("tabs",`
 box-sizing: border-box;
 width: 100%;
 display: flex;
 flex-direction: column;
 transition:
 background-color .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
`,[w("segment-type",[p("tabs-rail",[O("&.transition-disabled",[p("tabs-capsule",`
 transition: none;
 `)])])]),w("top",[p("tab-pane",`
 padding: var(--n-pane-padding-top) var(--n-pane-padding-right) var(--n-pane-padding-bottom) var(--n-pane-padding-left);
 `)]),w("left",[p("tab-pane",`
 padding: var(--n-pane-padding-right) var(--n-pane-padding-bottom) var(--n-pane-padding-left) var(--n-pane-padding-top);
 `)]),w("left, right",`
 flex-direction: row;
 `,[p("tabs-bar",`
 width: 2px;
 right: 0;
 transition:
 top .2s var(--n-bezier),
 max-height .2s var(--n-bezier),
 background-color .3s var(--n-bezier);
 `),p("tabs-tab",`
 padding: var(--n-tab-padding-vertical); 
 `)]),w("right",`
 flex-direction: row-reverse;
 `,[p("tab-pane",`
 padding: var(--n-pane-padding-left) var(--n-pane-padding-top) var(--n-pane-padding-right) var(--n-pane-padding-bottom);
 `),p("tabs-bar",`
 left: 0;
 `)]),w("bottom",`
 flex-direction: column-reverse;
 justify-content: flex-end;
 `,[p("tab-pane",`
 padding: var(--n-pane-padding-bottom) var(--n-pane-padding-right) var(--n-pane-padding-top) var(--n-pane-padding-left);
 `),p("tabs-bar",`
 top: 0;
 `)]),p("tabs-rail",`
 position: relative;
 padding: 3px;
 border-radius: var(--n-tab-border-radius);
 width: 100%;
 background-color: var(--n-color-segment);
 transition: background-color .3s var(--n-bezier);
 display: flex;
 align-items: center;
 `,[p("tabs-capsule",`
 border-radius: var(--n-tab-border-radius);
 position: absolute;
 pointer-events: none;
 background-color: var(--n-tab-color-segment);
 box-shadow: 0 1px 3px 0 rgba(0, 0, 0, .08);
 transition: transform 0.3s var(--n-bezier);
 `),p("tabs-tab-wrapper",`
 flex-basis: 0;
 flex-grow: 1;
 display: flex;
 align-items: center;
 justify-content: center;
 `,[p("tabs-tab",`
 overflow: hidden;
 border-radius: var(--n-tab-border-radius);
 width: 100%;
 display: flex;
 align-items: center;
 justify-content: center;
 `,[w("active",`
 font-weight: var(--n-font-weight-strong);
 color: var(--n-tab-text-color-active);
 `),O("&:hover",`
 color: var(--n-tab-text-color-hover);
 `)])])]),w("flex",[p("tabs-nav",`
 width: 100%;
 position: relative;
 `,[p("tabs-wrapper",`
 width: 100%;
 `,[p("tabs-tab",`
 margin-right: 0;
 `)])])]),p("tabs-nav",`
 box-sizing: border-box;
 line-height: 1.5;
 display: flex;
 transition: border-color .3s var(--n-bezier);
 `,[U("prefix, suffix",`
 display: flex;
 align-items: center;
 `),U("prefix","padding-right: 16px;"),U("suffix","padding-left: 16px;")]),w("top, bottom",[O(">",[p("tabs-nav",[p("tabs-nav-scroll-wrapper",[O("&::before",`
 top: 0;
 bottom: 0;
 left: 0;
 width: 20px;
 `),O("&::after",`
 top: 0;
 bottom: 0;
 right: 0;
 width: 20px;
 `),w("shadow-start",[O("&::before",`
 box-shadow: inset 10px 0 8px -8px rgba(0, 0, 0, .12);
 `)]),w("shadow-end",[O("&::after",`
 box-shadow: inset -10px 0 8px -8px rgba(0, 0, 0, .12);
 `)])])])])]),w("left, right",[p("tabs-nav-scroll-content",`
 flex-direction: column;
 `),O(">",[p("tabs-nav",[p("tabs-nav-scroll-wrapper",[O("&::before",`
 top: 0;
 left: 0;
 right: 0;
 height: 20px;
 `),O("&::after",`
 bottom: 0;
 left: 0;
 right: 0;
 height: 20px;
 `),w("shadow-start",[O("&::before",`
 box-shadow: inset 0 10px 8px -8px rgba(0, 0, 0, .12);
 `)]),w("shadow-end",[O("&::after",`
 box-shadow: inset 0 -10px 8px -8px rgba(0, 0, 0, .12);
 `)])])])])]),p("tabs-nav-scroll-wrapper",`
 flex: 1;
 position: relative;
 overflow: hidden;
 `,[p("tabs-nav-y-scroll",`
 height: 100%;
 width: 100%;
 overflow-y: auto; 
 scrollbar-width: none;
 `,[O("&::-webkit-scrollbar, &::-webkit-scrollbar-track-piece, &::-webkit-scrollbar-thumb",`
 width: 0;
 height: 0;
 display: none;
 `)]),O("&::before, &::after",`
 transition: box-shadow .3s var(--n-bezier);
 pointer-events: none;
 content: "";
 position: absolute;
 z-index: 1;
 `)]),p("tabs-nav-scroll-content",`
 display: flex;
 position: relative;
 min-width: 100%;
 min-height: 100%;
 width: fit-content;
 box-sizing: border-box;
 `),p("tabs-wrapper",`
 display: inline-flex;
 flex-wrap: nowrap;
 position: relative;
 `),p("tabs-tab-wrapper",`
 display: flex;
 flex-wrap: nowrap;
 flex-shrink: 0;
 flex-grow: 0;
 `),p("tabs-tab",`
 cursor: pointer;
 white-space: nowrap;
 flex-wrap: nowrap;
 display: inline-flex;
 align-items: center;
 color: var(--n-tab-text-color);
 font-size: var(--n-tab-font-size);
 background-clip: padding-box;
 padding: var(--n-tab-padding);
 transition:
 box-shadow .3s var(--n-bezier),
 color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
 `,[w("disabled",{cursor:"not-allowed"}),U("close",`
 margin-left: 6px;
 transition:
 background-color .3s var(--n-bezier),
 color .3s var(--n-bezier);
 `),U("label",`
 display: flex;
 align-items: center;
 z-index: 1;
 `)]),p("tabs-bar",`
 position: absolute;
 bottom: 0;
 height: 2px;
 border-radius: 1px;
 background-color: var(--n-bar-color);
 transition:
 left .2s var(--n-bezier),
 max-width .2s var(--n-bezier),
 opacity .3s var(--n-bezier),
 background-color .3s var(--n-bezier);
 `,[O("&.transition-disabled",`
 transition: none;
 `),w("disabled",`
 background-color: var(--n-tab-text-color-disabled)
 `)]),p("tabs-pane-wrapper",`
 position: relative;
 overflow: hidden;
 transition: max-height .2s var(--n-bezier);
 `),p("tab-pane",`
 color: var(--n-pane-text-color);
 width: 100%;
 transition:
 color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 opacity .2s var(--n-bezier);
 left: 0;
 right: 0;
 top: 0;
 `,[O("&.next-transition-leave-active, &.prev-transition-leave-active, &.next-transition-enter-active, &.prev-transition-enter-active",`
 transition:
 color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 transform .2s var(--n-bezier),
 opacity .2s var(--n-bezier);
 `),O("&.next-transition-leave-active, &.prev-transition-leave-active",`
 position: absolute;
 `),O("&.next-transition-enter-from, &.prev-transition-leave-to",`
 transform: translateX(32px);
 opacity: 0;
 `),O("&.next-transition-leave-to, &.prev-transition-enter-from",`
 transform: translateX(-32px);
 opacity: 0;
 `),O("&.next-transition-leave-from, &.next-transition-enter-to, &.prev-transition-leave-from, &.prev-transition-enter-to",`
 transform: translateX(0);
 opacity: 1;
 `)]),p("tabs-tab-pad",`
 box-sizing: border-box;
 width: var(--n-tab-gap);
 flex-grow: 0;
 flex-shrink: 0;
 `),w("line-type, bar-type",[p("tabs-tab",`
 font-weight: var(--n-tab-font-weight);
 box-sizing: border-box;
 vertical-align: bottom;
 `,[O("&:hover",{color:"var(--n-tab-text-color-hover)"}),w("active",`
 color: var(--n-tab-text-color-active);
 font-weight: var(--n-tab-font-weight-active);
 `),w("disabled",{color:"var(--n-tab-text-color-disabled)"})])]),p("tabs-nav",[w("line-type",[w("top",[U("prefix, suffix",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),p("tabs-nav-scroll-content",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),p("tabs-bar",`
 bottom: -1px;
 `)]),w("left",[U("prefix, suffix",`
 border-right: 1px solid var(--n-tab-border-color);
 `),p("tabs-nav-scroll-content",`
 border-right: 1px solid var(--n-tab-border-color);
 `),p("tabs-bar",`
 right: -1px;
 `)]),w("right",[U("prefix, suffix",`
 border-left: 1px solid var(--n-tab-border-color);
 `),p("tabs-nav-scroll-content",`
 border-left: 1px solid var(--n-tab-border-color);
 `),p("tabs-bar",`
 left: -1px;
 `)]),w("bottom",[U("prefix, suffix",`
 border-top: 1px solid var(--n-tab-border-color);
 `),p("tabs-nav-scroll-content",`
 border-top: 1px solid var(--n-tab-border-color);
 `),p("tabs-bar",`
 top: -1px;
 `)]),U("prefix, suffix",`
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-nav-scroll-content",`
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-bar",`
 border-radius: 0;
 `)]),w("card-type",[U("prefix, suffix",`
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-pad",`
 flex-grow: 1;
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-tab-pad",`
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-tab",`
 font-weight: var(--n-tab-font-weight);
 border: 1px solid var(--n-tab-border-color);
 background-color: var(--n-tab-color);
 box-sizing: border-box;
 position: relative;
 vertical-align: bottom;
 display: flex;
 justify-content: space-between;
 font-size: var(--n-tab-font-size);
 color: var(--n-tab-text-color);
 `,[w("addable",`
 padding-left: 8px;
 padding-right: 8px;
 font-size: 16px;
 justify-content: center;
 `,[U("height-placeholder",`
 width: 0;
 font-size: var(--n-tab-font-size);
 `),ja("disabled",[O("&:hover",`
 color: var(--n-tab-text-color-hover);
 `)])]),w("closable","padding-right: 8px;"),w("active",`
 background-color: #0000;
 font-weight: var(--n-tab-font-weight-active);
 color: var(--n-tab-text-color-active);
 `),w("disabled","color: var(--n-tab-text-color-disabled);")])]),w("left, right",`
 flex-direction: column; 
 `,[U("prefix, suffix",`
 padding: var(--n-tab-padding-vertical);
 `),p("tabs-wrapper",`
 flex-direction: column;
 `),p("tabs-tab-wrapper",`
 flex-direction: column;
 `,[p("tabs-tab-pad",`
 height: var(--n-tab-gap-vertical);
 width: 100%;
 `)])]),w("top",[w("card-type",[p("tabs-scroll-padding","border-bottom: 1px solid var(--n-tab-border-color);"),U("prefix, suffix",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),p("tabs-tab",`
 border-top-left-radius: var(--n-tab-border-radius);
 border-top-right-radius: var(--n-tab-border-radius);
 `,[w("active",`
 border-bottom: 1px solid #0000;
 `)]),p("tabs-tab-pad",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),p("tabs-pad",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `)])]),w("left",[w("card-type",[p("tabs-scroll-padding","border-right: 1px solid var(--n-tab-border-color);"),U("prefix, suffix",`
 border-right: 1px solid var(--n-tab-border-color);
 `),p("tabs-tab",`
 border-top-left-radius: var(--n-tab-border-radius);
 border-bottom-left-radius: var(--n-tab-border-radius);
 `,[w("active",`
 border-right: 1px solid #0000;
 `)]),p("tabs-tab-pad",`
 border-right: 1px solid var(--n-tab-border-color);
 `),p("tabs-pad",`
 border-right: 1px solid var(--n-tab-border-color);
 `)])]),w("right",[w("card-type",[p("tabs-scroll-padding","border-left: 1px solid var(--n-tab-border-color);"),U("prefix, suffix",`
 border-left: 1px solid var(--n-tab-border-color);
 `),p("tabs-tab",`
 border-top-right-radius: var(--n-tab-border-radius);
 border-bottom-right-radius: var(--n-tab-border-radius);
 `,[w("active",`
 border-left: 1px solid #0000;
 `)]),p("tabs-tab-pad",`
 border-left: 1px solid var(--n-tab-border-color);
 `),p("tabs-pad",`
 border-left: 1px solid var(--n-tab-border-color);
 `)])]),w("bottom",[w("card-type",[p("tabs-scroll-padding","border-top: 1px solid var(--n-tab-border-color);"),U("prefix, suffix",`
 border-top: 1px solid var(--n-tab-border-color);
 `),p("tabs-tab",`
 border-bottom-left-radius: var(--n-tab-border-radius);
 border-bottom-right-radius: var(--n-tab-border-radius);
 `,[w("active",`
 border-top: 1px solid #0000;
 `)]),p("tabs-tab-pad",`
 border-top: 1px solid var(--n-tab-border-color);
 `),p("tabs-pad",`
 border-top: 1px solid var(--n-tab-border-color);
 `)])])])]),Bt=zn,$n=Object.assign(Object.assign({},da.props),{value:[String,Number],defaultValue:[String,Number],trigger:{type:String,default:"click"},type:{type:String,default:"bar"},closable:Boolean,justifyContent:String,size:String,placement:{type:String,default:"top"},tabStyle:[String,Object],tabClass:String,addTabStyle:[String,Object],addTabClass:String,barWidth:Number,paneClass:String,paneStyle:[String,Object],paneWrapperClass:String,paneWrapperStyle:[String,Object],addable:[Boolean,Object],tabsPadding:{type:Number,default:0},animated:Boolean,onBeforeLeave:Function,onAdd:Function,"onUpdate:value":[Function,Array],onUpdateValue:[Function,Array],onClose:[Function,Array],labelSize:String,activeName:[String,Number],onActiveNameChange:[Function,Array]}),Ln=ct({name:"Tabs",props:$n,slots:Object,setup(e,{slots:n}){var r,d,o,f;const{mergedClsPrefixRef:g,inlineThemeDisabled:k,mergedComponentPropsRef:x}=Fa(e),P=da("Tabs","-tabs",An,qa,e,g),z=h(null),A=h(null),F=h(null),M=h(null),B=h(null),K=h(null),q=h(!0),W=h(!0),E=Yt(e,["labelSize","size"]),te=S(()=>{var u,l;if(E.value)return E.value;const b=(l=(u=x?.value)===null||u===void 0?void 0:u.Tabs)===null||l===void 0?void 0:l.size;return b||"medium"}),V=Yt(e,["activeName","value"]),T=h((d=(r=V.value)!==null&&r!==void 0?r:e.defaultValue)!==null&&d!==void 0?d:n.default?(f=(o=At(n.default())[0])===null||o===void 0?void 0:o.props)===null||f===void 0?void 0:f.name:null),_=pn(V,T),j={id:0},G=S(()=>{if(!(!e.justifyContent||e.type==="card"))return{display:"flex",justifyContent:e.justifyContent}});De(_,()=>{j.id=0,ae(),Ge()});function ce(){var u;const{value:l}=_;return l===null?null:(u=z.value)===null||u===void 0?void 0:u.querySelector(`[data-name="${l}"]`)}function ft(u){if(e.type==="card")return;const{value:l}=A;if(!l)return;const b=l.style.opacity==="0";if(u){const C=`${g.value}-tabs-bar--disabled`,{barWidth:$,placement:Q}=e;if(u.dataset.disabled==="true"?l.classList.add(C):l.classList.remove(C),["top","bottom"].includes(Q)){if(Ve(["top","maxHeight","height"]),typeof $=="number"&&u.offsetWidth>=$){const H=Math.floor((u.offsetWidth-$)/2)+u.offsetLeft;l.style.left=`${H}px`,l.style.maxWidth=`${$}px`}else l.style.left=`${u.offsetLeft}px`,l.style.maxWidth=`${u.offsetWidth}px`;l.style.width="8192px",b&&(l.style.transition="none"),l.offsetWidth,b&&(l.style.transition="",l.style.opacity="1")}else{if(Ve(["left","maxWidth","width"]),typeof $=="number"&&u.offsetHeight>=$){const H=Math.floor((u.offsetHeight-$)/2)+u.offsetTop;l.style.top=`${H}px`,l.style.maxHeight=`${$}px`}else l.style.top=`${u.offsetTop}px`,l.style.maxHeight=`${u.offsetHeight}px`;l.style.height="8192px",b&&(l.style.transition="none"),l.offsetHeight,b&&(l.style.transition="",l.style.opacity="1")}}}function fe(){if(e.type==="card")return;const{value:u}=A;u&&(u.style.opacity="0")}function Ve(u){const{value:l}=A;if(l)for(const b of u)l.style[b]=""}function ae(){if(e.type==="card")return;const u=ce();u?ft(u):fe()}function Ge(){var u;const l=(u=B.value)===null||u===void 0?void 0:u.$el;if(!l)return;const b=ce();if(!b)return;const{scrollLeft:C,offsetWidth:$}=l,{offsetLeft:Q,offsetWidth:H}=b;C>Q?l.scrollTo({top:0,left:Q,behavior:"smooth"}):Q+H>C+$&&l.scrollTo({top:0,left:Q+H-$,behavior:"smooth"})}const Te=h(null);let Pe=0,X=null;function Y(u){const l=Te.value;if(l){Pe=u.getBoundingClientRect().height;const b=`${Pe}px`,C=()=>{l.style.height=b,l.style.maxHeight=b};X?(C(),X(),X=null):X=C}}function R(u){const l=Te.value;if(l){const b=u.getBoundingClientRect().height,C=()=>{document.body.offsetHeight,l.style.maxHeight=`${b}px`,l.style.height=`${Math.max(Pe,b)}px`};X?(X(),X=null,C()):X=C}}function oe(){const u=Te.value;if(u){u.style.maxHeight="",u.style.height="";const{paneWrapperStyle:l}=e;if(typeof l=="string")u.style.cssText=l;else if(l){const{maxHeight:b,height:C}=l;b!==void 0&&(u.style.maxHeight=b),C!==void 0&&(u.style.height=C)}}}const Re={value:[]},We=h("next");function ze(u){const l=_.value;let b="next";for(const C of Re.value){if(C===l)break;if(C===u){b="prev";break}}We.value=b,ye(u)}function ye(u){const{onActiveNameChange:l,onUpdateValue:b,"onUpdate:value":C}=e;l&&rt(l,u),b&&rt(b,u),C&&rt(C,u),T.value=u}function je(u){const{onClose:l}=e;l&&rt(l,u)}function Ie(){const{value:u}=A;if(!u)return;const l="transition-disabled";u.classList.add(l),ae(),u.classList.remove(l)}const ie=h(null);function Ae({transitionDisabled:u}){const l=z.value;if(!l)return;u&&l.classList.add("transition-disabled");const b=ce();b&&ie.value&&(ie.value.style.width=`${b.offsetWidth}px`,ie.value.style.height=`${b.offsetHeight}px`,ie.value.style.transform=`translateX(${b.offsetLeft-bn(getComputedStyle(l).paddingLeft)}px)`,u&&ie.value.offsetWidth),u&&l.classList.remove("transition-disabled")}De([_],()=>{e.type==="segment"&&It(()=>{Ae({transitionDisabled:!1})})}),ua(()=>{e.type==="segment"&&Ae({transitionDisabled:!0})});let Xe=0;function pt(u){var l;if(u.contentRect.width===0&&u.contentRect.height===0||Xe===u.contentRect.width)return;Xe=u.contentRect.width;const{type:b}=e;if((b==="line"||b==="bar")&&Ie(),b!=="segment"){const{placement:C}=e;me((C==="top"||C==="bottom"?(l=B.value)===null||l===void 0?void 0:l.$el:K.value)||null)}}const vt=Bt(pt,64);De([()=>e.justifyContent,()=>e.size],()=>{It(()=>{const{type:u}=e;(u==="line"||u==="bar")&&Ie()})});const be=h(!1);function bt(u){var l;const{target:b,contentRect:{width:C,height:$}}=u,Q=b.parentElement.parentElement.offsetWidth,H=b.parentElement.parentElement.offsetHeight,{placement:pe}=e;if(!be.value)pe==="top"||pe==="bottom"?Q<C&&(be.value=!0):H<$&&(be.value=!0);else{const{value:xe}=M;if(!xe)return;pe==="top"||pe==="bottom"?Q-C>xe.$el.offsetWidth&&(be.value=!1):H-$>xe.$el.offsetHeight&&(be.value=!1)}me(((l=B.value)===null||l===void 0?void 0:l.$el)||null)}const $e=Bt(bt,64);function Fe(){const{onAdd:u}=e;u&&u(),It(()=>{const l=ce(),{value:b}=B;!l||!b||b.scrollTo({left:l.offsetLeft,top:0,behavior:"smooth"})})}function me(u){if(!u)return;const{placement:l}=e;if(l==="top"||l==="bottom"){const{scrollLeft:b,scrollWidth:C,offsetWidth:$}=u;q.value=b<=0,W.value=b+$>=C}else{const{scrollTop:b,scrollHeight:C,offsetHeight:$}=u;q.value=b<=0,W.value=b+$>=C}}const Le=Bt(u=>{me(u.target)},64);Ia(Ft,{triggerRef:le(e,"trigger"),tabStyleRef:le(e,"tabStyle"),tabClassRef:le(e,"tabClass"),addTabStyleRef:le(e,"addTabStyle"),addTabClassRef:le(e,"addTabClass"),paneClassRef:le(e,"paneClass"),paneStyleRef:le(e,"paneStyle"),mergedClsPrefixRef:g,typeRef:le(e,"type"),closableRef:le(e,"closable"),valueRef:_,tabChangeIdRef:j,onBeforeLeaveRef:le(e,"onBeforeLeave"),activateTab:ze,handleClose:je,handleAdd:Fe}),vn(()=>{ae(),Ge()}),Sa(()=>{const{value:u}=F;if(!u)return;const{value:l}=g,b=`${l}-tabs-nav-scroll-wrapper--shadow-start`,C=`${l}-tabs-nav-scroll-wrapper--shadow-end`;q.value?u.classList.remove(b):u.classList.add(b),W.value?u.classList.remove(C):u.classList.add(C)});const Ee={syncBarPosition:()=>{ae()}},qe=()=>{Ae({transitionDisabled:!0})},he=S(()=>{const{value:u}=te,{type:l}=e,b={card:"Card",bar:"Bar",line:"Line",segment:"Segment"}[l],C=`${u}${b}`,{self:{barColor:$,closeIconColor:Q,closeIconColorHover:H,closeIconColorPressed:pe,tabColor:xe,tabBorderColor:Qe,paneTextColor:mt,tabFontWeight:gt,tabBorderRadius:se,tabFontWeightActive:Je,colorSegment:Ye,fontWeightStrong:Ze,tabColorSegment:ke,closeSize:yt,closeIconSize:ht,closeColorHover:xt,closeColorPressed:et,closeBorderRadius:Ue,[ue("panePadding",u)]:Ke,[ue("tabPadding",C)]:kt,[ue("tabPaddingVertical",C)]:tt,[ue("tabGap",C)]:_t,[ue("tabGap",`${C}Vertical`)]:at,[ue("tabTextColor",l)]:Ct,[ue("tabTextColorActive",l)]:wt,[ue("tabTextColorHover",l)]:St,[ue("tabTextColorDisabled",l)]:Tt,[ue("tabFontSize",u)]:Pt},common:{cubicBezierEaseInOut:Rt}}=P.value;return{"--n-bezier":Rt,"--n-color-segment":Ye,"--n-bar-color":$,"--n-tab-font-size":Pt,"--n-tab-text-color":Ct,"--n-tab-text-color-active":wt,"--n-tab-text-color-disabled":Tt,"--n-tab-text-color-hover":St,"--n-pane-text-color":mt,"--n-tab-border-color":Qe,"--n-tab-border-radius":se,"--n-close-size":yt,"--n-close-icon-size":ht,"--n-close-color-hover":xt,"--n-close-color-pressed":et,"--n-close-border-radius":Ue,"--n-close-icon-color":Q,"--n-close-icon-color-hover":H,"--n-close-icon-color-pressed":pe,"--n-tab-color":xe,"--n-tab-font-weight":gt,"--n-tab-font-weight-active":Je,"--n-tab-padding":kt,"--n-tab-padding-vertical":tt,"--n-tab-gap":_t,"--n-tab-gap-vertical":at,"--n-pane-padding-left":ut(Ke,"left"),"--n-pane-padding-right":ut(Ke,"right"),"--n-pane-padding-top":ut(Ke,"top"),"--n-pane-padding-bottom":ut(Ke,"bottom"),"--n-font-weight-strong":Ze,"--n-tab-color-segment":ke}}),ne=k?Ua("tabs",S(()=>`${te.value[0]}${e.type[0]}`),he,e):void 0;return Object.assign({mergedClsPrefix:g,mergedValue:_,renderedNames:new Set,segmentCapsuleElRef:ie,tabsPaneWrapperRef:Te,tabsElRef:z,barElRef:A,addTabInstRef:M,xScrollInstRef:B,scrollWrapperElRef:F,addTabFixed:be,tabWrapperStyle:G,handleNavResize:vt,mergedSize:te,handleScroll:Le,handleTabsResize:$e,cssVars:k?void 0:he,themeClass:ne?.themeClass,animationDirection:We,renderNameListRef:Re,yScrollElRef:K,handleSegmentResize:qe,onAnimationBeforeLeave:Y,onAnimationEnter:R,onAnimationAfterEnter:oe,onRender:ne?.onRender},Ee)},render(){const{mergedClsPrefix:e,type:n,placement:r,addTabFixed:d,addable:o,mergedSize:f,renderNameListRef:g,onRender:k,paneWrapperClass:x,paneWrapperStyle:P,$slots:{default:z,prefix:A,suffix:F}}=this;k?.();const M=z?At(z()).filter(T=>T.type.__TAB_PANE__===!0):[],B=z?At(z()).filter(T=>T.type.__TAB__===!0):[],K=!B.length,q=n==="card",W=n==="segment",E=!q&&!W&&this.justifyContent;g.value=[];const te=()=>{const T=v("div",{style:this.tabWrapperStyle,class:`${e}-tabs-wrapper`},E?null:v("div",{class:`${e}-tabs-scroll-padding`,style:r==="top"||r==="bottom"?{width:`${this.tabsPadding}px`}:{height:`${this.tabsPadding}px`}}),K?M.map((_,j)=>(g.value.push(_.props.name),Nt(v(Wt,Object.assign({},_.props,{internalCreatedByPane:!0,internalLeftPadded:j!==0&&(!E||E==="center"||E==="start"||E==="end")}),_.children?{default:_.children.tab}:void 0)))):B.map((_,j)=>(g.value.push(_.props.name),Nt(j!==0&&!E?ra(_):_))),!d&&o&&q?na(o,(K?M.length:B.length)!==0):null,E?null:v("div",{class:`${e}-tabs-scroll-padding`,style:{width:`${this.tabsPadding}px`}}));return v("div",{ref:"tabsElRef",class:`${e}-tabs-nav-scroll-content`},q&&o?v(Kt,{onResize:this.handleTabsResize},{default:()=>T}):T,q?v("div",{class:`${e}-tabs-pad`}):null,q?null:v("div",{ref:"barElRef",class:`${e}-tabs-bar`}))},V=W?"top":r;return v("div",{class:[`${e}-tabs`,this.themeClass,`${e}-tabs--${n}-type`,`${e}-tabs--${f}-size`,E&&`${e}-tabs--flex`,`${e}-tabs--${V}`],style:this.cssVars},v("div",{class:[`${e}-tabs-nav--${n}-type`,`${e}-tabs-nav--${V}`,`${e}-tabs-nav`]},Ht(A,T=>T&&v("div",{class:`${e}-tabs-nav__prefix`},T)),W?v(Kt,{onResize:this.handleSegmentResize},{default:()=>v("div",{class:`${e}-tabs-rail`,ref:"tabsElRef"},v("div",{class:`${e}-tabs-capsule`,ref:"segmentCapsuleElRef"},v("div",{class:`${e}-tabs-wrapper`},v("div",{class:`${e}-tabs-tab`}))),K?M.map((T,_)=>(g.value.push(T.props.name),v(Wt,Object.assign({},T.props,{internalCreatedByPane:!0,internalLeftPadded:_!==0}),T.children?{default:T.children.tab}:void 0))):B.map((T,_)=>(g.value.push(T.props.name),_===0?T:ra(T))))}):v(Kt,{onResize:this.handleNavResize},{default:()=>v("div",{class:`${e}-tabs-nav-scroll-wrapper`,ref:"scrollWrapperElRef"},["top","bottom"].includes(V)?v(fn,{ref:"xScrollInstRef",onScroll:this.handleScroll},{default:te}):v("div",{class:`${e}-tabs-nav-y-scroll`,onScroll:this.handleScroll,ref:"yScrollElRef"},te()))}),d&&o&&q?na(o,!0):null,Ht(F,T=>T&&v("div",{class:`${e}-tabs-nav__suffix`},T))),K&&(this.animated&&(V==="top"||V==="bottom")?v("div",{ref:"tabsPaneWrapperRef",style:P,class:[`${e}-tabs-pane-wrapper`,x]},aa(M,this.mergedValue,this.renderedNames,this.onAnimationBeforeLeave,this.onAnimationEnter,this.onAnimationAfterEnter,this.animationDirection)):aa(M,this.mergedValue,this.renderedNames)))}});function aa(e,n,r,d,o,f,g){const k=[];return e.forEach(x=>{const{name:P,displayDirective:z,"display-directive":A}=x.props,F=B=>z===B||A===B,M=n===P;if(x.key!==void 0&&(x.key=P),M||F("show")||F("show:lazy")&&r.has(P)){r.has(P)||r.add(P);const B=!F("if");k.push(B?Ta(x,[[Pa,M]]):x)}}),g?v(Ra,{name:`${g}-transition`,onBeforeLeave:d,onEnter:o,onAfterEnter:f},{default:()=>k}):k}function na(e,n){return v(Wt,{ref:"addTabInstRef",key:"__addable",name:"__addable",internalCreatedByPane:!0,internalAddable:!0,internalLeftPadded:n,disabled:typeof e=="object"&&e.disabled})}function ra(e){const n=za(e);return n.props?n.props.internalLeftPadded=!0:n.props={internalLeftPadded:!0},n}function Nt(e){return Array.isArray(e.dynamicProps)?e.dynamicProps.includes("internalLeftPadded")||e.dynamicProps.push("internalLeftPadded"):e.dynamicProps=["internalLeftPadded"],e}function En(e){return ee("GET","/api/keys",{signal:e})}function Kn(e){return ee("POST","/api/keys",{body:e})}function On(e){return ee("GET","/api/keys/"+e+"/view")}function Bn(e){return ee("GET","/api/keys/"+e+"/usage")}function Nn(e,n={}){return ee("GET","/api/keys/"+e+"/ips",{query:{days:n.days??1,sort:n.sort??"requests",direction:n.direction??"desc",limit:n.limit??100,offset:n.offset??0}})}function Mt(e,n){return ee("PATCH","/api/keys/"+e,{body:n})}function Mn(e){return ee("DELETE","/api/keys/"+e)}function Wn(){return ee("GET","/api/credits/selection-policy")}function jn(){return ee("GET","/api/app/network")}function Fn(){return ee("GET","/api/models/aggregate")}function qn(){return ee("GET","/api/groups")}function Un(e={}){return ee("GET","/api/accounts",{query:{all_platforms:1,page:e.page??1,page_size:e.pageSize??50,search:e.search||""}})}function Hn(){return ee("GET","/api/platforms")}function Dn(e){return(Math.round(e*1e3)/10).toFixed(1)+"%"}function Vn(e){const n=!!e.unlimited,r=Number(e.credit_limit||0),d=Number(e.credit_used||0),o=n||r<=0?0:Math.min(1,Math.max(0,d/r)),f=n?"ok":o>=.9?"danger":o>=.6?"warn":"ok",g=e.ip_summary||{days:1,unique_ips:0,requests:0,active_ips:0,active_requests:0},k=e.max_concurrent??null,x=e.rate_per_minute??null,P=Array.isArray(e.account_ids)?e.account_ids.map(Number).filter(A=>A>0):[],z=e.masked||(e.key_prefix?un(e.key_prefix):Me);return{id:e.id,name:e.name||"未命名",masked:z,keyPrefix:e.key_prefix||"",limitText:n?"∞":Se(r),usedText:Se(d),usageRatio:o,usageTone:f,usageText:n?"不限额度":Dn(o),unlimited:n,concurrencyText:k===0?"不限":k==null?"全局":String(k),rateText:x===0?"不限频率":x==null?"频率随全局":x+" 次/分",ipText:(Number(g.unique_ips)||0)+" 个 / 24h",ipBadges:(g.top_ips||[]).slice(0,3).map(A=>({ip:A.ip,text:A.ip+" · "+(Number(A.active_requests)||0)+" 并发 · "+(Number(A.requests)||0)+" 次"})),groupLabel:e.group_name||"全部模型",statusLabel:e.status==="active"?"正常":"已停用",statusTone:e.status==="active"?"ok":"neutral",accountLabel:P.length?"独占 "+P.length+" 个账号":e.account_id?"独占账号："+(e.account_name||"#"+e.account_id):"公共账号池",expireText:Me,note:e.note||"",platform:e.platform||"",aggregate:!!e.aggregate,groupId:Number(e.group_id||0),createdAt:e.created_at||"",status:e.status,creditLimit:r,creditUsed:d,maxConcurrent:k,ratePerMinute:x,accountIds:P,accountId:e.account_id,accountName:e.account_name||"",ipSummary:g}}function Gn(e){return Array.isArray(e)?e.map(Vn):[]}function Xn(e,n){const r=String(n.q||"").trim().toLowerCase(),d=String(n.group||""),o=String(n.status||"");return e.filter(f=>!(r&&![f.name,f.note,f.accountName,f.platform].join(" ").toLowerCase().includes(r)||d==="none"&&f.groupId!==0||d&&d!=="none"&&String(f.groupId)!==d||o&&f.status!==o))}function Qn(e,n,r){if(!n||r!=="asc"&&r!=="desc")return e;const d=r==="asc"?1:-1,o=f=>n==="name"?0:n==="limit"?f.unlimited?Number.MAX_SAFE_INTEGER:f.creditLimit:n==="used"?f.creditUsed:n==="concurrency"?f.maxConcurrent==null?-1:f.maxConcurrent:n==="created"&&Date.parse(f.createdAt||"")||0;return[...e].sort((f,g)=>n==="name"?f.name.localeCompare(g.name,"zh-CN")*d:(o(f)-o(g))*d)}function Jn(e,n,r){const d=Math.max(1,Math.floor(r||20)),o=e.length,f=Math.max(1,Math.ceil(o/d)),g=Math.min(Math.max(1,Math.floor(n||1)),f);return{items:e.slice((g-1)*d,g*d),total:o,page:g,pageCount:f}}function oa(e){return{total:e.length,active:e.filter(n=>n.status==="active").length,revoked:e.filter(n=>n.status!=="active").length,unlimited:e.filter(n=>n.unlimited).length}}function Yn(e){return{status:e==="active"?"revoked":"active"}}function jt(e,n){const r=String(e||""),d=String(n||"");return!r||!d||r.toLowerCase().endsWith("-"+d.toLowerCase())?r:r+"-"+d}function Zn(e,n=12){const r=(e?.items||[]).filter(o=>(o.platforms||[]).some(f=>f.enabled!==!1)),d=r.slice(0,Math.max(0,n)).map(o=>{const f=(o.platforms||[]).filter(x=>x.enabled!==!1),g=f[0],k=(f.length?f.map(x=>{const P=x.client_id||jt(x.model_id,x.platform),z=x.multiplier==null?Me:Number(x.multiplier)===0?"FREE":"×"+x.multiplier;return(x.platform_label||x.platform)+" · "+P+" · "+z}).join(`
`):"暂无平台明细")+`
点击复制调用名（带平台后缀）`;return{key:o.key||String(o.name),name:o.name,platformCount:f.length||Number(o.platform_count||0),copyValue:g?g.client_id||jt(g.model_id,g.platform):"",title:k}});return{chips:d,total:r.length,rest:Math.max(0,r.length-d.length)}}function er(e,n){const r=String(n||"").trim().toLowerCase();return(e?.items||[]).filter(d=>(d.platforms||[]).some(o=>o.enabled!==!1)).map(d=>{const o=(d.platforms||[]).filter(f=>f.enabled!==!1);return{key:d.key||String(d.name),name:d.name,platformCount:o.length,rows:o.map(f=>({platform:f.platform,platformLabel:f.platform_label||f.platform,callId:f.client_id||jt(f.model_id,f.platform),upstreamId:f.model_id,multiplierText:f.multiplier==null?Me:Number(f.multiplier)===0?"FREE":"×"+f.multiplier}))}}).filter(d=>!r||d.name.toLowerCase().includes(r)?!0:d.rows.some(o=>o.callId.toLowerCase().includes(r)||o.upstreamId.toLowerCase().includes(r)))}function tr(e,n){const r=new Map;n.origin&&r.set(n.origin,"当前访问 · "+(n.origin.replace(/^https?:\/\//,"")||"当前域名"));const d=["localhost","127.0.0.1"].includes(n.hostname),o=n.port||(d&&e?.port?String(e.port):""),f=o?":"+o:"";r.set("http://127.0.0.1"+f,"本机 · 127.0.0.1"+f);for(const g of e?.lan_ips||[])r.set("http://"+g+f,"局域网 · "+g+f);return[...r].map(([g,k])=>({value:g,label:k}))}function ar(e,n){const r=(e||"").replace(/\/+$/,"")+"/v1",d=(e||"").replace(/\/+$/,""),o=n||"auto";return{openai:["curl "+r+"/chat/completions \\",'  -H "Authorization: Bearer <你的 Key>" \\','  -H "Content-Type: application/json" \\',`  -d '{"model":"`+o+`","messages":[{"role":"user","content":"你好"}]}'`].join(`
`),claude:["curl "+d+"/v1/messages \\",'  -H "x-api-key: <你的 Key>" \\','  -H "anthropic-version: 2023-06-01" \\','  -H "Content-Type: application/json" \\',`  -d '{"model":"claude-sonnet-4-5-20250929","max_tokens":1024,"messages":[{"role":"user","content":"你好"}]}'`].join(`
`),audioCurl:['curl "'+r+'/audio/transcriptions" \\','  -H "Authorization: Bearer <你的 Key>" \\','  -F "file=@recording.webm" \\','  -F "model=workbuddy-asr" \\','  -F "response_format=json"'].join(`
`),audioPython:["from openai import OpenAI",'client = OpenAI(api_key="<你的 Key>", base_url="'+r+'")','with open("recording.webm", "rb") as audio:','    result = client.audio.transcriptions.create(model="workbuddy-asr", file=audio)',"print(result.text)"].join(`
`),imagesPython:["from openai import OpenAI",'client = OpenAI(api_key="<你的 Key>", base_url="'+r+'")','result = client.images.generate(model="hunyuan-image-alpha", prompt="白色背景上的蓝色圆形", n=1)',"print(result.data[0].url)",'with open("input.png", "rb") as image:','    edited = client.images.edit(model="hunyuan-image-alpha", image=image, prompt="把圆形改成绿色")',"print(edited.data[0].url)"].join(`
`),videoCurl:["# 1) 创建任务：立即返回 queued，不阻塞等待生成",'curl "'+r+'/videos" \\','  -H "Authorization: Bearer <你的 Key>" \\','  -H "Content-Type: application/json" \\',`  -d '{"model":"kling-video-v3","prompt":"一只猫在弹钢琴","seconds":"4","size":"1280x720"}'`,"","# 2) 轮询（用上一步返回的 id），直到 status=completed",'curl "'+r+'/videos/<视频 ID>" -H "Authorization: Bearer <你的 Key>"',"","# 3) 下载 MP4",'curl "'+r+'/videos/<视频 ID>/content" -H "Authorization: Bearer <你的 Key>" -o output.mp4'].join(`
`),videoPython:["import time","from openai import OpenAI",'client = OpenAI(api_key="<你的 Key>", base_url="'+r+'")',"",'video = client.videos.create(model="kling-video-v3", prompt="一只猫在弹钢琴", seconds="4", size="1280x720")',"print(video.id, video.status)          # queued","",'while video.status not in ("completed", "failed"):',"    time.sleep(5)","    video = client.videos.retrieve(video.id)","",'if video.status == "completed":',"    content = client.videos.download_content(video.id)",'    content.write_to_file("output.mp4")'].join(`
`)}}function ia(e,n){const r=String(e||"").trim();if(!r)return;const d=Number(r);if(!Number.isFinite(d))return n+"必须是数字";if(d<0)return n+"不能小于 0";if(!Number.isInteger(d))return n+"必须是整数"}function nr(e,n){const r={};if(n&&!String(e.name||"").trim()&&(r.name="名称不能为空"),!e.unlimited){const d=String(e.creditLimit||"").trim();if(d){const o=Number(d);Number.isFinite(o)?o<0&&(r.creditLimit="额度不能小于 0"):r.creditLimit="额度必须是数字"}}return r.maxConcurrent=ia(e.maxConcurrent,"并发上限"),r.ratePerMinute=ia(e.ratePerMinute,"每分钟请求数"),r}function rr(e){return Object.values(e).some(Boolean)}function or(e){const n=d=>{const o=String(d||"").trim();return o===""?null:Number(o)},r=String(e.creditLimit||"").trim();return{name:String(e.name||"").trim(),note:String(e.note||""),group_id:Number(e.groupId||0),unlimited:e.unlimited||r==="",credit_limit:r===""?null:Number(r),max_concurrent:n(e.maxConcurrent),rate_per_minute:n(e.ratePerMinute),aggregate:!!e.aggregate,platform:String(e.platform||"").toLowerCase(),account_ids:e.accountIds.slice()}}function ir(e){const n=!!e.unlimited,r=e.usage_percent,d=n||r==null?0:Math.min(1,Math.max(0,r/100));return{limitText:n?"∞（无限制）":Se(e.credit_limit),usedText:Se(e.credit_used),remainingText:n||e.credit_remaining==null?"∞":Se(e.credit_remaining),percentText:r==null?"N/A":r.toFixed(1)+"%",ratio:d,tone:n?"ok":d>=.9?"danger":d>=.6?"warn":"ok",unlimited:n,createdText:e.created_at?ca(e.created_at):Me}}function sa(e){return{totalIps:Number(e.total_ips||0),requests:Number(e.total_requests||0),activeIps:Number(e.active_ips||0),activeRequests:Number(e.active_requests||0),rows:(e.items||[]).map(n=>({ip:n.ip,requests:Number(n.requests||0),failed:Number(n.failed||0),credits:Number(n.credits||0),tokens:Number(n.tokens||0),lastSeen:n.last_seen?ca(n.last_seen):Me,models:(n.models||[]).map(r=>r.model).filter(Boolean).join("、"),active:Number(n.active_requests||0),blocked:!!n.blocked}))}}const sr={class:"keys-page"},lr={class:"keys-policy"},ur={class:"keys-share__grid"},dr={class:"keys-share__row"},cr={class:"keys-share__row"},fr={class:"keys-share__row"},pr={class:"keys-share__row"},vr={class:"keys-share__row keys-share__row--models"},br={class:"keys-share__models"},mr={class:"keys-muted"},gr={key:0,class:"keys-chip-count"},yr={key:1,class:"keys-muted"},hr={class:"keys-filter"},xr={class:"keys-body"},kr={key:1,class:"keys-skeleton","aria-busy":"true","aria-label":"Key 加载中"},_r={class:"keys-form"},Cr={class:"keys-form"},wr={class:"keys-form__inline"},Sr={class:"keys-form"},Tr={class:"keys-modal__footer"},Pr={key:1,class:"keys-skeleton"},Rr={class:"keys-usage-grid"},zr={class:"keys-usage-cell"},Ir={class:"tnum"},Ar={class:"keys-usage-cell"},$r={class:"tnum"},Lr={class:"keys-usage-cell"},Er={class:"tnum"},Kr={class:"keys-usage-cell"},Or={class:"tnum"},Br={class:"keys-hint"},Nr={class:"keys-ipbar"},Mr={key:0,class:"keys-muted"},Wr={class:"keys-model__head"},jr={class:"keys-muted"},Fr={class:"keys-model__rows"},qr={class:"keys-muted"},Ur={class:"keys-muted tnum"},Hr="对外共享的调用凭证：每个 Key 单独计量、限额与封禁，共享出去不影响后台登录。OpenAI 与 Anthropic 两套协议共用同一批 Key、同一套配额与用量记账，只是 base_url 约定不同：OpenAI SDK 填含 /v1 的那个，Claude Code / Anthropic SDK 填不含 /v1 的那个（SDK 会自己拼 /v1/messages，填错会变成 /v1/v1/messages）。",Dr="OpenAI 与 Anthropic 两套协议共用同一批 Key、同一套配额与用量记账，只是 base_url 约定不同：OpenAI SDK 填 OpenAI 地址（含 /v1），Claude Code / Anthropic SDK 填 Claude 地址（不含 /v1，SDK 会自己拼 /v1/messages，填错会变成 /v1/v1/messages）。Claude 端的 claude-sonnet-4-5 这类模型名由服务端自动按档次映射到白名单里的模型，无需手动改。",Vr=ct({__name:"KeysPage",setup(e){const n=$a(),r=Xa(),d=sn(),o=ln("keys",{pageSize:20,filters:{q:"",group:"",status:""}}),f=h(null),g=h(!1),k=h(null);let x=null;const P=S(()=>Gn(f.value)),z=S(()=>Xn(P.value,o.state.filters)),A=S(()=>Qn(z.value,o.state.sortBy,o.state.sortOrder)),F=S(()=>Jn(A.value,o.state.page,o.state.pageSize)),M=S(()=>oa(P.value)),B=S(()=>g.value&&f.value===null),K=S(()=>!!(o.state.filters.q||o.state.filters.group||o.state.filters.status)),q=S(()=>{const a=oa(z.value);return K.value?"筛选出 "+a.total+" / 共 "+M.value.total+" 个 Key · 正常 "+a.active:"共 "+a.total+" 个 Key · 正常 "+a.active+" · 已停用 "+a.revoked+" · 不限额 "+a.unlimited}),W=S(()=>K.value?"没有符合条件的 Key。清空筛选后可看到全部 Key。":"还没有 Key。创建 Key 后即可对外调用网关。"),E=h(null),te=h(null),V=h(null),T=h(!1),_={origin:typeof window<"u"?window.location.origin:"",hostname:typeof window<"u"?window.location.hostname:"",port:typeof window<"u"?window.location.port:""},j=S(()=>tr(te.value,_)),G=h(_.origin);De(j,a=>{a.length&&!a.some(t=>t.value===G.value)&&(G.value=a[0].value)});const ce=S(()=>(G.value||"").replace(/\/+$/,"")+"/v1"),ft=S(()=>(G.value||"").replace(/\/+$/,"")),fe=S(()=>Zn(V.value,12)),Ve=S(()=>fe.value.chips[0]?.copyValue||""),ae=S(()=>ar(G.value,Ve.value)),Ge=S(()=>(E.value?.modes||[]).find(t=>t.value===E.value?.mode)?.label||"账号调度策略"),Te=S(()=>((E.value?.modes||[]).find(t=>t.value===E.value?.mode)?.description||"决定网关挑哪一个账号承接请求。")+"（在「积分到期」页修改）");function Pe(){return{name:"",note:"",groupId:0,unlimited:!0,creditLimit:"",maxConcurrent:"",ratePerMinute:"",aggregate:!0,platform:"",accountIds:[],resetUsed:!1}}const X=h(!1),Y=h(null),R=h(Pe()),oe=h({}),Re=h(!1),We=h(""),ze=h(!1),ye=h([]),je=h([]),Ie=h([]),ie=h(!1);let Ae=!1;const Xe=S(()=>[{label:"不限制（全部可用模型）",value:0},...ye.value.map(a=>({label:a.name+(a.effective_count!=null?"（"+a.effective_count+" 个模型）":""),value:a.id}))]),pt=S(()=>[{label:"不限制（全部平台）",value:""},...je.value.map(a=>({label:a.label,value:a.storage}))]),vt=S(()=>Ie.value.map(a=>({label:(a.name||a.uid||"账号")+" #"+a.id+(a.status&&a.status!=="active"?" · 已禁用":"")+(a.balance_remain!=null?" · "+Se(a.balance_remain)+" 积分":""),value:a.id}))),be=S(()=>Y.value?"编辑 API Key":"创建 API Key"),bt=S(()=>Y.value?"保存":"创建"),$e=h(!1),Fe=h(!1),me=h(""),Le=h(null),Ee=h(!1),qe=h(!1),he=h(""),ne=h("1"),u=h(""),l=h([]),b=h(sa({total_ips:0,total_requests:0,active_ips:0,active_requests:0,items:[]}));let C=0;const $=h(!1),Q=h(""),H=h(!1),pe=h("openai"),xe=S(()=>[{key:"ip",title:"IP 地址",mobile:"title",width:150},{key:"requests",title:"调用次数",mobile:"meta",width:100,numeric:!0,render:a=>ge(a.requests)},{key:"failed",title:"失败",mobile:"meta",width:80,numeric:!0,render:a=>ge(a.failed)},{key:"credits",title:"积分",mobile:"meta",width:90,numeric:!0,render:a=>Se(a.credits)},{key:"tokens",title:"Token",mobile:"meta",width:100,numeric:!0,render:a=>ge(a.tokens)},{key:"lastSeen",title:"最近调用",mobile:"meta",width:150},{key:"models",title:"使用模型",mobile:"subtitle",minWidth:180},{key:"active",title:"并发",mobile:"meta",width:80,numeric:!0,render:a=>ge(a.active)},{key:"blocked",title:"状态",mobile:"meta",width:90,render:a=>v(it,{tone:a.blocked?"danger":"ok"},{default:()=>a.blocked?"已封禁":"正常"})}]),Qe=S(()=>er(V.value,Q.value)),mt=S(()=>l.value);function gt(a){if(typeof a=="string")return a;try{return JSON.stringify(a,null,2)}catch{return String(a)}}async function se(){x?.abort();const a=new AbortController;x=a,g.value=!0,k.value=null;try{const t=await En(a.signal);f.value=Array.isArray(t?.items)?t.items:[];const i=Math.max(1,Math.ceil(z.value.length/o.state.pageSize));o.state.page>i&&o.setPage(i)}catch(t){if(an(t))return;k.value={message:Et(t),detail:t instanceof nn&&t.detail?gt(t.detail):""},r.error(Oe(f.value===null?"加载 Key":"刷新 Key",t))}finally{x===a&&(x=null,g.value=!1)}}async function Je(){Wn().then(a=>{E.value=a}).catch(()=>{E.value=null}),jn().then(a=>{te.value=a}).catch(()=>{te.value=null}),T.value=!0;try{V.value=await Fn()}catch{V.value=null}finally{T.value=!1}}async function Ye(a){ie.value=!0;try{const t=await Un({search:a,pageSize:50});Ie.value=Array.isArray(t?.items)?t.items:t?.accounts||[]}catch{Ie.value=[]}finally{ie.value=!1}}async function Ze(){Ae||(Ae=!0,qn().then(a=>{ye.value=Array.isArray(a?.items)?a.items:[]}).catch(()=>{ye.value=[]}),Hn().then(a=>{const t=new Map;for(const i of a?.items||[]){const I=String(i.storage_platform||i.id||"").toLowerCase();!I||t.has(I)||t.set(I,String(i.label||I))}je.value=[...t].map(([i,I])=>({storage:i,label:I}))}).catch(()=>{je.value=[]}),await Ye(""))}const ke=h(o.state.filters.q);De(()=>o.state.filters.q,a=>{ke.value=a});const yt=[{label:"状态：全部",value:""},{label:"正常",value:"active"},{label:"已停用",value:"revoked"}],ht=S(()=>[{label:"分组：全部",value:""},{label:"未绑定分组",value:"none"},...ye.value.map(a=>({label:a.name,value:String(a.id)}))]),xt=S(()=>{const a=o.state.filters,t=[];if(a.q&&t.push({key:"q",label:"搜索",value:a.q}),a.group){const i=a.group==="none"?"未绑定分组":ye.value.find(I=>String(I.id)===a.group)?.name||a.group;t.push({key:"group",label:"分组",value:i})}return a.status&&t.push({key:"status",label:"状态",value:a.status==="active"?"正常":"已停用"}),t}),et={name:"name",limit:"limit",used:"used",concurrency:"concurrency"};function Ue(a){return o.state.sortBy!==a?!1:o.state.sortOrder==="asc"?"ascend":o.state.sortOrder==="desc"?"descend":!1}function Ke(a){if(!a||!a.order||!et[String(a.columnKey)]){o.setSort("","");return}o.setSort(et[String(a.columnKey)],a.order==="ascend"?"asc":"desc")}function kt(a){const t={};for(const[i,I]of Object.entries(a))t[i]=String(I??"");o.setFilters(t)}function tt(a,t){o.setFilter(a,t==null?"":String(t))}function _t(a){a==="q"&&(ke.value=""),o.setFilter(a,"")}function at(){ke.value="",o.reset()}function Ct(a){const t=a;return v("div",{class:"keys-name"},[v("span",{class:"keys-name__main",title:t.name},t.name),v("span",{class:"keys-name__sub"},t.accountLabel)])}function wt(a){const t=a;return v("div",{class:"keys-key"},[v("code",{class:"mono keys-key__text",title:t.masked},t.masked),v(Ja,{icon:Va,label:"复制完整 Key",onClick:()=>{qt(t)}})])}function St(a){const t=a;return v("div",{class:"keys-quota"},[v("div",{class:"keys-quota__line tnum"},[v("span",{},t.usedText),v("span",{class:"keys-quota__sep"}," / "),v("span",{},t.limitText)]),t.unlimited?v("span",{class:"keys-quota__hint"},"不限额"):v(Qt,{value:t.usageRatio*100,tone:t.usageTone,height:4,ariaLabel:"额度使用率"})])}function Tt(a){const t=a;return v("div",{class:"keys-concurrency"},[v("span",{class:"tnum"},t.concurrencyText),v("span",{class:"keys-concurrency__rate"},t.rateText)])}function Pt(a){const t=a;return v("div",{class:"keys-ips"},[v("button",{type:"button",class:"keys-ips__count",title:"查看该 Key 的来源 IP 与请求次数，用于识别共享范围与异常来源",onClick:()=>{_a(t)}},t.ipText),t.ipBadges.length?v("div",{class:"keys-ips__badges"},t.ipBadges.map(i=>v(it,{key:i.ip,tone:"brand",title:i.text},{default:()=>i.ip}))):null])}function Rt(a){const t=a;return t.groupId?v(it,{tone:"brand",title:"该 Key 仅可使用所绑定分组内的模型"},{default:()=>t.groupLabel}):v("span",{class:"keys-muted"},t.groupLabel)}function pa(a){return v("span",{class:"keys-muted",title:"后端 Key 模型没有到期字段，到期时间未提供"},a.expireText)}function va(a){const t=a;return v(it,{tone:t.statusTone,dot:!0},{default:()=>t.statusLabel})}function ba(a){const t=a;return v(tn,{actions:[{key:"edit",label:"编辑"},{key:"copy",label:"复制 Key"},{key:"reset",label:"重置用量"},{key:"toggle",label:t.status==="active"?"停用":"启用"},{key:"usage",label:"查看用量"},{key:"delete",label:"删除",danger:!0}],max:2,onSelect:i=>{xa(t,i)}})}const ma=S(()=>[{key:"name",title:"名称",mobile:"title",minWidth:180,sortable:!0,sortOrder:Ue("name"),render:Ct},{key:"key",title:"Key",mobile:"subtitle",minWidth:220,render:wt},{key:"quota",title:"限额 / 已用",mobile:"meta",minWidth:170,sortable:!0,sortOrder:Ue("used"),render:St},{key:"concurrency",title:"并发",mobile:"meta",width:120,sortable:!0,sortOrder:Ue("concurrency"),render:Tt},{key:"ips",title:"来源 IP",mobile:"meta",minWidth:170,render:Pt},{key:"group",title:"分组",mobile:"meta",width:130,render:Rt},{key:"expire",title:"到期",mobile:"meta",width:90,render:pa},{key:"status",title:"状态",mobile:"meta",width:96,render:va},{key:"actions",title:"操作",mobile:"actions",width:150,hideable:!1,fixed:"right",render:ba}]);function ga(a){const t=Array.isArray(a)?a:a===null||a===""?[]:[a];R.value.accountIds=t.map(Number).filter(i=>Number.isFinite(i)&&i>0)}async function qt(a){try{const t=await On(a.id);await Ga(t.key)?r.success("已复制完整 Key"):r.error("复制失败：浏览器拒绝了剪贴板访问，请手动选中复制")}catch(t){r.error(Oe("读取 Key",t))}}function Ut(){Y.value=null,R.value=Pe(),oe.value={},X.value=!0,Ze()}function ya(a){Y.value=a,R.value={name:a.name,note:a.note,groupId:a.groupId,unlimited:a.unlimited,creditLimit:a.unlimited?"":String(a.creditLimit),maxConcurrent:a.maxConcurrent==null?"":String(a.maxConcurrent),ratePerMinute:a.ratePerMinute==null?"":String(a.ratePerMinute),aggregate:a.aggregate,platform:a.platform,accountIds:a.accountIds.slice(),resetUsed:!1},oe.value={},X.value=!0,Ze()}async function ha(){if(oe.value=nr(R.value,!!Y.value),!rr(oe.value)){Re.value=!0;try{const a=or(R.value);if(Y.value)await Mt(Y.value.id,{...a,reset_used:R.value.resetUsed}),r.success("已保存"),X.value=!1,await se();else{const t=await Kn({...a,credit_limit:a.credit_limit??null});X.value=!1,We.value=t.key,ze.value=!0,await se()}}catch(a){r.error(Oe(Y.value?"保存 Key":"创建 Key",a))}finally{Re.value=!1}}}async function xa(a,t){if(t==="edit")return ya(a);if(t==="copy")return qt(a);if(t==="usage")return ka(a);if(t==="reset"){if(!await d({title:"重置已用额度？",message:"已用积分归零，历史日志保留。该 Key 立即恢复全部额度。",okText:"重置用量"}))return;try{await Mt(a.id,{reset_used:!0}),r.success("已重置已用额度"),await se()}catch(I){r.error(Oe("重置用量",I))}return}if(t==="toggle"){try{await Mt(a.id,Yn(a.status)),r.success(a.status==="active"?"已停用":"已启用"),await se()}catch(i){r.error(Oe(a.status==="active"?"停用 Key":"启用 Key",i))}return}if(t==="delete"){if(!await d({title:"删除该 Key？",message:"删除后该 Key 立即无法调用网关，用量记录保留。如需临时停止调用，请使用停用。",danger:!0,okText:"删除"}))return;try{await Mn(a.id),r.success("已删除"),await se()}catch(I){r.error(Oe("删除 Key",I))}}}async function ka(a){$e.value=!0,Fe.value=!0,me.value="",Le.value=null;try{Le.value=await Bn(a.id)}catch(t){me.value=Et(t)}finally{Fe.value=!1}}async function zt(){if(C){qe.value=!0,he.value="";try{const a=await Nn(C,{days:Number(ne.value)||1});l.value=a.items||[],b.value=sa(a)}catch(a){he.value=Et(a),l.value=[]}finally{qe.value=!1}}}async function _a(a){C=a.id,u.value=a.name,ne.value="1",l.value=[],Ee.value=!0,await zt()}const Ca=S(()=>[{label:"来源 IP",value:ge(b.value.totalIps)},{label:"调用次数",value:ge(b.value.requests)},{label:"当前活跃 IP",value:ge(b.value.activeIps)},{label:"当前并发",value:ge(b.value.activeRequests)}]),ve=S(()=>Le.value?ir(Le.value):null);return ua(()=>{se(),Je()}),Aa(()=>{x?.abort()}),(a,t)=>(L(),Z("div",sr,[c(Ha,{title:"API Keys",help:Hr},{actions:m(()=>[c(s(J),{variant:"quiet",icon:s(dn),loading:g.value,onClick:t[0]||(t[0]=i=>se())},{default:m(()=>[...t[35]||(t[35]=[N("刷新",-1)])]),_:1},8,["icon","loading"]),y("span",lr,[c(s(J),{variant:"quiet",onClick:t[1]||(t[1]=i=>s(n).push("/credits"))},{default:m(()=>[N(D(Ge.value),1)]),_:1}),c(s(Dt),{content:Te.value,label:"账号调度策略说明"},null,8,["content"])]),c(s(J),{variant:"default",onClick:t[2]||(t[2]=i=>H.value=!0)},{default:m(()=>[...t[36]||(t[36]=[N("接入指南",-1)])]),_:1}),c(s(J),{variant:"primary",icon:s(Jt),onClick:Ut},{default:m(()=>[...t[37]||(t[37]=[N("创建 Key",-1)])]),_:1},8,["icon"])]),_:1}),c(s(Vt),{class:"keys-share",title:"对接信息"},{extra:m(()=>[c(s(J),{variant:"quiet",size:"sm",loading:T.value,onClick:t[3]||(t[3]=i=>Je())},{default:m(()=>[...t[38]||(t[38]=[N("刷新",-1)])]),_:1},8,["loading"])]),default:m(()=>[y("div",ur,[y("div",dr,[t[39]||(t[39]=y("span",{class:"keys-share__label"},"连接地址",-1)),c(s(Be),{class:"keys-share__select",value:G.value,options:j.value,"onUpdate:value":t[4]||(t[4]=i=>G.value=i)},null,8,["value","options"])]),y("div",cr,[t[40]||(t[40]=y("span",{class:"keys-share__label"},[N("OpenAI"),y("em",null,"含 /v1")],-1)),c(s(st),{value:ce.value},null,8,["value"])]),y("div",fr,[t[41]||(t[41]=y("span",{class:"keys-share__label"},[N("Claude"),y("em",null,"不含 /v1")],-1)),c(s(st),{value:ft.value},null,8,["value"])]),y("div",pr,[t[42]||(t[42]=y("span",{class:"keys-share__label"},"鉴权",-1)),c(s(st),{value:"Authorization: Bearer <你的 Key>　或　x-api-key: <你的 Key>"}),c(s(Dt),{content:Dr,label:"鉴权说明"})]),y("div",vr,[t[43]||(t[43]=y("span",{class:"keys-share__label"},"可用模型",-1)),y("div",br,[y("span",mr,D(fe.value.total)+" 组",1),(L(!0),Z(Ne,null,nt(fe.value.chips,i=>(L(),re(s(Gt),{key:i.key,mono:"",copy:"",value:i.copyValue,title:i.title},{default:m(()=>[N(D(i.name),1),i.platformCount>1?(L(),Z("span",gr,D(i.platformCount),1)):_e("",!0)]),_:2},1032,["value","title"]))),128)),fe.value.total?(L(),re(s(J),{key:0,variant:"quiet",size:"sm",onClick:t[5]||(t[5]=i=>$.value=!0)},{default:m(()=>[N(" 全部 "+D(fe.value.total)+" ▸ ",1)]),_:1})):_e("",!0),!fe.value.total&&!T.value?(L(),Z("span",yr,"未配置模型白名单（放行全部模型）")):_e("",!0)])])])]),_:1}),c(s(Vt),{flush:"",class:"keys-card"},{default:m(()=>[y("div",hr,[c(s(en),{chips:xt.value,summary:q.value,loading:g.value,onChange:kt,onReset:at,onRemove:_t},{default:m(({change:i})=>[c(s(Ce),{class:"keys-filter__search",value:ke.value,placeholder:"搜索名称 / 备注 / 账号",clearable:"","onUpdate:value":I=>{ke.value=I,i("q",I)}},null,8,["value","onUpdate:value"]),c(s(Be),{class:"keys-filter__select",value:s(o).state.filters.group,options:ht.value,"onUpdate:value":t[6]||(t[6]=I=>tt("group",I))},null,8,["value","options"]),c(s(Be),{class:"keys-filter__select",value:s(o).state.filters.status,options:yt,"onUpdate:value":t[7]||(t[7]=I=>tt("status",I))},null,8,["value"])]),_:1},8,["chips","summary","loading"])]),y("div",xr,[k.value&&f.value===null?(L(),re(s($t),{key:0,title:"加载 Key 失败："+k.value.message,detail:k.value.detail,onRetry:t[8]||(t[8]=i=>se())},null,8,["title","detail"])):B.value?(L(),Z("div",kr,[(L(),Z(Ne,null,nt(6,i=>c(s(Zt),{key:i,height:"44px"})),64))])):(L(),re(s(Xt),{key:2,"table-id":"keys",columns:ma.value,data:F.value.items,"row-key":"id",remote:"",loading:g.value,page:s(o).state.page,"page-size":s(o).state.pageSize,"item-count":F.value.total,"page-sizes":[20,50,100],"empty-description":W.value,"onUpdate:page":s(o).setPage,"onUpdate:pageSize":s(o).setPageSize,"onUpdate:sorter":Ke},{empty:m(()=>[c(s(Qa),{description:W.value},{default:m(()=>[K.value?(L(),re(s(J),{key:0,variant:"default",size:"sm",onClick:at},{default:m(()=>[...t[44]||(t[44]=[N("清除筛选",-1)])]),_:1})):(L(),re(s(J),{key:1,variant:"primary",size:"sm",icon:s(Jt),onClick:Ut},{default:m(()=>[...t[45]||(t[45]=[N("创建 Key",-1)])]),_:1},8,["icon"]))]),_:1},8,["description"])]),_:1},8,["columns","data","loading","page","page-size","item-count","empty-description","onUpdate:page","onUpdate:pageSize"]))])]),_:1}),c(s(on),{show:X.value,"onUpdate:show":t[20]||(t[20]=i=>X.value=i),title:be.value,size:"lg","submit-text":bt.value,"submit-loading":Re.value,onSubmit:ha},{default:m(()=>[c(s(ot),{title:"基本",first:""},{default:m(()=>[y("div",_r,[c(s(de),{label:"名称",required:!!Y.value,error:oe.value.name,hint:"创建时可留空，后端会填「未命名」。"},{default:m(()=>[c(s(Ce),{value:R.value.name,"onUpdate:value":t[9]||(t[9]=i=>R.value.name=i),placeholder:"例如：给设计组的 Key",maxlength:"120",clearable:""},null,8,["value"])]),_:1},8,["required","error"]),c(s(de),{label:"模型分组",hint:"选择后该 Key 只能调用组内模型，调用组外模型返回 403。"},{default:m(()=>[c(s(Be),{value:R.value.groupId,"onUpdate:value":t[10]||(t[10]=i=>R.value.groupId=i),options:Xe.value},null,8,["value","options"])]),_:1}),c(s(de),{label:"备注"},{default:m(()=>[c(s(Ce),{value:R.value.note,"onUpdate:value":t[11]||(t[11]=i=>R.value.note=i),type:"textarea",rows:2,maxlength:"255",placeholder:"可选"},null,8,["value"])]),_:1})])]),_:1}),c(s(ot),{title:"额度与流量"},{default:m(()=>[y("div",Cr,[c(s(de),{label:"总额度（积分）",error:oe.value.creditLimit,hint:"勾选「不限额」时忽略；填 0 表示零额度。"},{default:m(()=>[y("div",wr,[c(s(Lt),{checked:R.value.unlimited,"onUpdate:checked":t[12]||(t[12]=i=>R.value.unlimited=i)},{default:m(()=>[...t[46]||(t[46]=[N("不限额（∞）",-1)])]),_:1},8,["checked"]),c(s(Ce),{value:R.value.creditLimit,"onUpdate:value":t[13]||(t[13]=i=>R.value.creditLimit=i),placeholder:"留空不限额；0 表示零额度",disabled:R.value.unlimited},null,8,["value","disabled"])])]),_:1},8,["error"]),c(s(de),{label:"并发上限",error:oe.value.maxConcurrent,hint:"空 = 跟随全局；0 = 不限；N = 该 Key 所有对话合计最多 N 个在途请求。"},{default:m(()=>[c(s(Ce),{value:R.value.maxConcurrent,"onUpdate:value":t[14]||(t[14]=i=>R.value.maxConcurrent=i),placeholder:"空 = 全局，0 = 不限"},null,8,["value"])]),_:1},8,["error"]),c(s(de),{label:"每分钟请求数",error:oe.value.ratePerMinute,hint:"频率与并发独立；积分不限额也不会解除流量限制。"},{default:m(()=>[c(s(Ce),{value:R.value.ratePerMinute,"onUpdate:value":t[15]||(t[15]=i=>R.value.ratePerMinute=i),placeholder:"空 = 全局，0 = 不限"},null,8,["value"])]),_:1},8,["error"]),Y.value?(L(),re(s(de),{key:0,label:"重置已用额度"},{default:m(()=>[c(s(Lt),{checked:R.value.resetUsed,"onUpdate:checked":t[16]||(t[16]=i=>R.value.resetUsed=i)},{default:m(()=>[N(" 勾选后把已用积分归零（当前 "+D(Y.value?.usedText)+"，不影响历史日志） ",1)]),_:1},8,["checked"])]),_:1})):_e("",!0)])]),_:1}),c(s(ot),{title:"模型与账号"},{default:m(()=>[y("div",Sr,[c(s(de),{label:"聚合模型路由",hint:"开启后 /v1/models 返回跨平台聚合列表，可由网关挑平台转发。"},{default:m(()=>[c(s(Lt),{checked:R.value.aggregate,"onUpdate:checked":t[17]||(t[17]=i=>R.value.aggregate=i)},{default:m(()=>[...t[47]||(t[47]=[N("使用聚合模型（跨平台负载分摊）",-1)])]),_:1},8,["checked"])]),_:1}),c(s(de),{label:"独占平台",hint:"绑定后只在该平台的账号池里选号；不限制则按请求自报平台。"},{default:m(()=>[c(s(Be),{value:R.value.platform,"onUpdate:value":t[18]||(t[18]=i=>R.value.platform=i),options:pt.value},null,8,["value","options"])]),_:1}),c(s(de),{label:"独占绑定账号（多选）",hint:"不选 = 使用公共账号池；选中后只用这些账号，账号不可用时不换号。搜索走服务端。"},{default:m(()=>[c(s(Be),{value:R.value.accountIds,multiple:"",filterable:"",remote:"",clearable:"",loading:ie.value,options:vt.value,placeholder:"不限制（使用公共账号池）","onUpdate:value":ga,onSearch:t[19]||(t[19]=i=>Ye(i))},null,8,["value","loading","options"])]),_:1})])]),_:1}),c(s(ot),{title:"访问与有效期"},{default:m(()=>[...t[48]||(t[48]=[y("p",{class:"keys-hint"}," 来源 IP 只用于统计（表格「来源 IP」列可查看），后端没有「绑定来源 IP」字段，因此这里不提供绑定位。 ",-1),y("p",{class:"keys-hint"},"后端 Key 模型没有到期字段，因此不提供有效期设置，表格「到期」列显示 —。",-1)])]),_:1})]),_:1},8,["show","title","submit-text","submit-loading"]),c(s(Da),{show:ze.value,"onUpdate:show":t[22]||(t[22]=i=>ze.value=i),preset:"card",class:"keys-modal",title:"Key 已创建",style:{width:"520px"}},{footer:m(()=>[y("div",Tr,[c(s(J),{variant:"primary",onClick:t[21]||(t[21]=i=>ze.value=!1)},{default:m(()=>[...t[49]||(t[49]=[N("完成",-1)])]),_:1})])]),default:m(()=>[t[50]||(t[50]=y("p",{class:"keys-hint"},"可随时在列表里点「复制 Key」再次查看完整值。",-1)),c(s(st),{value:We.value},null,8,["value"])]),_:1},8,["show"]),c(s(lt),{show:$e.value,"onUpdate:show":t[24]||(t[24]=i=>$e.value=i),title:"Key 用量",subtitle:"按额度统计"},{footer:m(()=>[c(s(J),{variant:"default",onClick:t[23]||(t[23]=i=>$e.value=!1)},{default:m(()=>[...t[55]||(t[55]=[N("关闭",-1)])]),_:1})]),default:m(()=>[me.value?(L(),re(s($t),{key:0,title:"读取用量失败："+me.value,retry:!1,compact:""},null,8,["title"])):Fe.value?(L(),Z("div",Pr,[c(s(Zt),{repeat:3,text:""})])):ve.value?(L(),Z(Ne,{key:2},[y("div",Rr,[y("div",zr,[t[51]||(t[51]=y("span",null,"限额",-1)),y("strong",Ir,D(ve.value.limitText),1)]),y("div",Ar,[t[52]||(t[52]=y("span",null,"已用",-1)),y("strong",$r,D(ve.value.usedText),1)]),y("div",Lr,[t[53]||(t[53]=y("span",null,"剩余",-1)),y("strong",Er,D(ve.value.remainingText),1)]),y("div",Kr,[t[54]||(t[54]=y("span",null,"使用率",-1)),y("strong",Or,D(ve.value.percentText),1)])]),ve.value.unlimited?_e("",!0):(L(),re(s(Qt),{key:0,value:ve.value.ratio*100,tone:ve.value.tone,height:8,"aria-label":"额度使用率"},null,8,["value","tone"])),y("p",Br,"创建于 "+D(ve.value.createdText),1)],64)):_e("",!0)]),_:1},8,["show"]),c(s(lt),{show:Ee.value,"onUpdate:show":t[28]||(t[28]=i=>Ee.value=i),title:"来源 IP · "+u.value,subtitle:"并发为当前服务进程的在途请求"},{footer:m(()=>[c(s(J),{variant:"default",onClick:t[27]||(t[27]=i=>Ee.value=!1)},{default:m(()=>[...t[57]||(t[57]=[N("关闭",-1)])]),_:1})]),default:m(()=>[y("div",Nr,[c(s(Ya),{options:[{label:"24 小时",value:"1"},{label:"7 天",value:"7"},{label:"30 天",value:"30"}],"model-value":ne.value,"aria-label":"时间范围","onUpdate:modelValue":t[25]||(t[25]=i=>{ne.value=String(i),zt()})},null,8,["model-value"]),c(s(J),{variant:"quiet",size:"sm",loading:qe.value,onClick:t[26]||(t[26]=i=>zt())},{default:m(()=>[...t[56]||(t[56]=[N("刷新",-1)])]),_:1},8,["loading"])]),c(s(Za),{items:Ca.value},null,8,["items"]),he.value?(L(),re(s($t),{key:0,title:"读取来源 IP 失败："+he.value,retry:!1,compact:""},null,8,["title"])):(L(),re(s(Xt),{key:1,"table-id":"keys-ips",columns:xe.value,data:mt.value,"row-key":"ip",pagination:!1,"virtual-scroll":!1,"max-height":"360px","empty-description":"所选时间内暂无调用记录"},null,8,["columns","data"])),t[58]||(t[58]=y("p",{class:"keys-hint"},"封禁 / 解封在「IP 管理」页操作；这里只读展示统计。",-1))]),_:1},8,["show","title"]),c(s(lt),{show:$.value,"onUpdate:show":t[31]||(t[31]=i=>$.value=i),title:"可用模型 · "+fe.value.total+" 组",subtitle:"点击模型名复制调用名（带平台后缀）"},{footer:m(()=>[c(s(J),{variant:"default",onClick:t[30]||(t[30]=i=>$.value=!1)},{default:m(()=>[...t[59]||(t[59]=[N("关闭",-1)])]),_:1})]),default:m(()=>[c(s(Ce),{value:Q.value,"onUpdate:value":t[29]||(t[29]=i=>Q.value=i),placeholder:"搜索模型名 / 调用名 / 上游 id",clearable:""},null,8,["value"]),Qe.value.length?_e("",!0):(L(),Z("div",Mr,"没有匹配的模型。")),(L(!0),Z(Ne,null,nt(Qe.value,i=>(L(),Z("div",{key:i.key,class:"keys-model"},[y("div",Wr,[y("strong",null,D(i.name),1),y("span",jr,D(i.platformCount)+" 平台",1)]),y("div",Fr,[(L(!0),Z(Ne,null,nt(i.rows,I=>(L(),Z("div",{key:I.callId,class:"keys-model__row"},[y("span",qr,D(I.platformLabel),1),c(s(Gt),{mono:"",copy:"",value:I.callId,title:"上游 id："+I.upstreamId},{default:m(()=>[N(D(I.callId),1)]),_:2},1032,["value","title"]),y("span",Ur,D(I.multiplierText),1)]))),128))])]))),128))]),_:1},8,["show","title"]),c(s(lt),{show:H.value,"onUpdate:show":t[34]||(t[34]=i=>H.value=i),title:"接入指南",subtitle:"地址与模型已按当前连接地址填好"},{footer:m(()=>[c(s(J),{variant:"default",onClick:t[33]||(t[33]=i=>H.value=!1)},{default:m(()=>[...t[63]||(t[63]=[N("关闭",-1)])]),_:1})]),default:m(()=>[c(s(Ln),{value:pe.value,"onUpdate:value":t[32]||(t[32]=i=>pe.value=i),type:"line",animated:""},{default:m(()=>[c(s(He),{name:"openai",tab:"OpenAI 协议"},{default:m(()=>[c(s(we),{code:ae.value.openai,language:"bash"},null,8,["code"])]),_:1}),c(s(He),{name:"claude",tab:"Claude Code / Anthropic"},{default:m(()=>[c(s(we),{code:ae.value.claude,language:"bash"},null,8,["code"])]),_:1}),c(s(He),{name:"audio",tab:"语音转文字"},{default:m(()=>[t[60]||(t[60]=y("p",{class:"keys-hint"}," POST /v1/audio/transcriptions，使用同一 API Key：multipart/form-data 上传 file， model=workbuddy-asr；response_format 可选 json（默认）或 text。网关文件上限 25 MiB， 不支持 language / prompt / 时间戳 / 流式转写；不保存音频与转写内容。 ",-1)),c(s(we),{code:ae.value.audioCurl,language:"bash"},null,8,["code"]),c(s(we),{code:ae.value.audioPython,language:"python"},null,8,["code"])]),_:1}),c(s(He),{name:"images",tab:"AI 生图"},{default:m(()=>[t[61]||(t[61]=y("p",{class:"keys-hint"}," 文生图 POST /v1/images/generations，图生图 POST /v1/images/edits（单张 image，最大 20 MiB； 也支持 image_url / base64 image_data）。返回 url（默认）或 b64_json；暂不支持 mask、流式、多图。 ",-1)),c(s(we),{code:ae.value.imagesPython,language:"python"},null,8,["code"])]),_:1}),c(s(He),{name:"videos",tab:"AI 生视频（异步任务）"},{default:m(()=>[t[62]||(t[62]=y("p",{class:"keys-hint"}," 创建 POST /v1/videos 只到「上游已入队」就返回 queued；轮询 GET /v1/videos/{id}， 下载 GET /v1/videos/{id}/content。生成通常需要 1~5 分钟，按上游 usage.credit 实际积分记账。 ",-1)),c(s(we),{code:ae.value.videoCurl,language:"bash"},null,8,["code"]),c(s(we),{code:ae.value.videoPython,language:"python"},null,8,["code"])]),_:1})]),_:1},8,["value"])]),_:1},8,["show"])]))}}),Oo=rn(Vr,[["__scopeId","data-v-062c0fc1"]]);export{Oo as default};
